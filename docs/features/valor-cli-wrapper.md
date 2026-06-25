# `valor` CLI: The Agent-Session Wrapper

**Status**: Shipped (PR #1612, on `session/granite-pty-production-cutover`)

## Problem

`valor-session` is the canonical interface for managing AgentSessions, but its
CLI is verbose for the common case. Spinning up a session that "just does this
thing" requires:

```bash
valor-session create --role eng --message "fix the typo in app.py"
```

Three flags and a subcommand for the most frequent operation. The shell also
ships a stale `valor` alias that points to a deleted `./scripts/telegram_run.sh`,
so the natural name for the tool is already taken by a broken link.

## What Shipped

A thin wrapper at `tools/valor_cli.py` (267 lines, ruff-formatted) installed as
`valor` in `pyproject.toml [project.scripts]`. It is a **pure delegation layer**:
every subcommand calls into the matching `cmd_*` in `tools.valor_session` and
exits with the same return code. No new abstractions, no duplicated state, no
schema changes.

The interface:

```bash
# Create — single positional prompt, defaults to eng role.
# Eng sessions REQUIRE a slug: pass --slug, or include
# "issue #N" in the prompt so the slug auto-derives to sdlc-N
# (issues #1109 / #1272 — slugless invocations exit 1).
valor "plan issue #1615"
valor "fix the typo in app.py" --slug typo-fix
valor agent-session --role eng --model sonnet --slug feature-x "build the feature"

# Lifecycle
valor list                          # recent 20 sessions
valor list --status running         # filtered
valor status <id>                   # one session
valor status <id> --full-message    # without 100-char truncation
valor steer <id> "stop after critique"
valor kill <id>
valor kill --all                    # nuclear

# Inspection / repair
valor inspect <id>                  # raw fields
valor children <id>                 # child sessions
valor resume <id> "new message"     # hard-PATCH resume
valor release --pr 1615             # clear retain_for_resume after PR merge/close
```

Two equivalent invocations for create:

- `valor "fix the bug" --slug fix-bug` — positional shortcut (preferred for humans and agents)
- `valor agent-session "fix the bug" --slug fix-bug` — explicit subcommand (preferred for scripts that need tab-completion or unambiguous error messages)

The wrapper detects the shortcut by sniffing argv: if the first token is not a
known subcommand or a flag, it is prepended with `agent-session` before
argparse runs. The first token is checked against the module-level
`KNOWN_SUBCOMMANDS` set (9 names; anything starting with `-` is already
excluded by the flag check), so an accidental prompt that starts with
`--kill-the-bug` doesn't get mangled. A unit test asserts the set stays in
sync with the subparser declarations.

## How It Works Well

### 1. Zero new behavior, zero new state

The wrapper does not persist anything, does not parse or interpret the prompt,
does not invent flags. It re-shapes argparse input and forwards to the
existing `cmd_create`, `cmd_status`, `cmd_list`, `cmd_steer`, `cmd_kill`,
`cmd_resume`, `cmd_inspect`, `cmd_children`, `cmd_release` functions. Bug
surfaces, help text, and JSON output formats stay in one place.

This means every fix to `valor-session` automatically applies to `valor`. And
every quirk of `valor-session` is preserved — including the project-key
resolution from `projects.json`, the auto-slug derivation from `issue #N` in
the message, the role-typed Model writes, and the worker pre-flight check.

### 2. Argparse namespace translation is a one-line mapping per subcommand

Each subcommand has a `_to_<cmd>_namespace` helper that copies fields from the
`valor` argparse Namespace into the shape `valor-session` expects. The helpers
are mechanical (8 functions, 4-9 lines each), so the translation cost is paid
once and stays there. If a flag is renamed on the underlying CLI, the diff
shows up in exactly one helper.

### 3. Smoke-testable in 10 lines

```bash
valor "smoke test: confirm the valor CLI wrapper is on the cutover branch" --role eng --slug valor-smoke
```

The dev session appears on the dashboard, the worker picks it up, and the
session is observable through `valor status <id>`. No new test harness needed
to verify end-to-end behavior.

### 4. Project-key and slug resolution still inherited from cwd

`valor` does not take a `--cwd` or `--worktree` flag. The session's
`working_dir` is still derived from `projects.json[project_key].working_directory`,
exactly as in `valor-session create`. This means an agent in any worktree
transparently runs the session in the right repo, and the slug-based worktree
isolation (issue #887) still applies when `--slug` is passed.

### 5. Compatible with both human and agent invocation

The wrapper is `user-invocable`-equivalent: humans reach for it by muscle
memory, and PM/Teammate sessions can call it the same way. No need to teach
agents a second interface — the agent-facing pattern in `pyproject.toml`
(`valor-foo = "tools.foo:main"`) already covers this.

## Where It Falls Short

### 1. The `valor` shell alias shadowing the venv binary — fixed

The stale `alias valor=…` line that pointed to the deleted
`./scripts/telegram_run.sh` has been removed from the origin machine. The
`/update` verify step now runs `check_valor_alias_shadow()` in
`scripts/update/verify.py` on every machine. It performs a warn-only static
scan of `~/.zshrc` for any non-comment line matching `^\s*alias\s+valor\s*=`,
and emits a copy-paste fix in the warning message when found. No machine
should encounter the stale alias going forward.

### 2. The positional-shortcut disambiguation is a literal allowlist — resolved (#1620)

`KNOWN_SUBCOMMANDS` is no longer a hand-maintained literal set. It is now
DERIVED at import time from the registered subparsers via
`_derive_known_subcommands()`, which reads `_SubParsersAction.choices.keys()`.
`_build_parser()` is decorated with `@functools.lru_cache` so the import-time
derivation and every runtime `main()` call share a single parser build.

Adding a new subparser to `_build_parser` now automatically extends the
allowlist — there is no parallel literal to keep in sync.

`tests/unit/test_valor_cli.py::TestKnownSubcommandsParity` was updated to
verify the derivation (the public constant equals the subparser registry and
equals `_derive_known_subcommands()`), not a literal-vs-registry parity check.

### 3. Worker pre-flight false negatives — fixed

The false warnings had two root causes, both now resolved:

1. **Worktree path divergence.** `_check_worker_health()` resolved the
   heartbeat file path relative to its own `__file__` location. When called
   from a worktree, that resolved to a path that either didn't exist or pointed
   at a stale copy, making the worker appear down even when healthy.

2. **Thin threshold margin.** The worker writes its heartbeat every 300 seconds;
   the CLI threshold was 360 seconds — a 60-second margin that any minor
   scheduling jitter could blow through.

The fix: `_resolve_heartbeat_path()` now resolves the heartbeat file via
`git rev-parse --path-format=absolute --git-common-dir` (with a
`__file__`-relative fallback), so the correct file is found from any worktree.
The threshold is now a single constant `WORKER_DOWN_THRESHOLD_S = 600` (2×
write cadence) in `agent/constants.py`, shared by both `valor-session` and
`tools/agent_session_scheduler.py`. The warning text never claims a created
session won't run.

The `--json` output from `cmd_create` and `cmd_status` now carries two fields
alongside `worker_healthy`: `worker_state` ("ok" or "down") and
`worker_heartbeat_age_s` (the raw age in seconds, clamped to 0). See
[Session Steering](session-steering.md) for the full worker pre-flight check
semantics.

### 4. No help text on the positional shortcut — resolved (#1620)

`main()` now has a help short-circuit that runs on the PRE-REWRITE argv —
before the positional injection that would turn `valor "fix the bug"` into
`valor agent-session "fix the bug"`. The guard fires when all three
conditions hold:

1. `argv` is non-empty.
2. `argv[0]` does not start with `-` (it is a bare prompt, not a flag).
3. `argv[0]` is not in `KNOWN_SUBCOMMANDS`.
4. A standalone `-h` or `--help` token appears anywhere in `argv` (exact
   element match, not substring — see below).

When it fires the top-level `valor --help` text is printed and
`SystemExit(0)` is raised.

Concretely: `valor "fix the bug" --help` now prints top-level help instead
of the `agent-session` create sub-help that argparse would have shown after
positional injection. `valor list --help` is unaffected — `list` is a known
subcommand and the guard does not fire.

**One accepted edge case.** "Standalone token" means an exact `argv` element
equal to `-h` or `--help`, not a substring. `valor "document the --help
flag"` is a single `argv` element and does NOT trigger the guard — the
prompt is delivered verbatim. The rare collision (`valor "some prompt"
--help` when the user genuinely wanted to create a session) is an accepted
tradeoff consistent with argparse's greedy-help convention.

### 5. PTY substrate selection is a worker concern (design boundary)

The CLI's job is to enqueue a session. The worker selects the execution
substrate (granite PTY container, `agent/granite_container/`) based on
environment — not the CLI. This is the correct separation: the CLI
knows nothing about how sessions are executed, and the worker knows nothing
about how sessions are created.

Post-cutover (#1572 / PR #1612) the granite PTY container is the only
execution substrate — all sessions route through it, and no alternate path
remains. A "force-the-other-substrate" env knob is therefore not applicable:
there is nothing to switch to. This is not a reserved future idea; it is a
closed question.

`valor "do the thing"` guarantees a session is enqueued and will be run on
the granite PTY substrate. It does not need to name the substrate to deliver
that guarantee.

### 6. Pre-commit hook guard #1288 — resolved (#1620)

The `.githooks/pre-commit` Phase 0.5 guard now implements option (a),
**allow-when-no-worktree**. On a `session/{slug}` branch committed from
outside the owning worktree, the guard checks whether `.worktrees/{slug}/`
exists on disk:

- **Worktree does NOT exist** → the main checkout is the only workspace for
  this slug; there is nothing to contaminate. The commit is ALLOWED with an
  informational note on stderr referencing #1620.
- **Worktree DOES exist** → the commit is BLOCKED exactly as before. The
  operator must commit from inside the worktree (cd `.worktrees/{slug}/` and
  commit there).

This is zero operator friction: the guard self-detects — no environment
variable to remember, no manual `git worktree add` dance needed when no
worktree exists. It never bypasses an existing worktree, so the
agent-contamination case that #887 and #1288 guard against stays blocked.

### 7. The slug requirement contradicts the "no boilerplate" pitch

The wrapper's whole point is `valor "do the thing"` — but the default role
is `pm`, and `cmd_create` rejects slugless PM/dev sessions (issues #1109,
#1272). So the shortest honest create is `valor "plan issue #1615"` (slug
auto-derives) or `valor "do the thing" --slug thing`. Only `--role teammate`
truly works with a bare prompt. This is the underlying CLI's (correct)
isolation policy showing through the wrapper, not a wrapper bug — but the
wrapper's help text and this doc must keep saying it loudly, because the
failure (`exit 1` with a stderr explanation) is the FIRST experience most
users will have with the bare-prompt form.

### 8. Per-session `--model` is currently ignored by the granite substrate

`valor agent-session --model sonnet ...` stores `model` on the AgentSession,
and the executor resolves it (`_resolve_session_model`) — but the granite
PTY path runs on pool-prewarmed PTYs whose models were fixed at spawn time
from `GRANITE__PM_MODEL` / `GRANITE__DEV_MODEL`. The resolved per-session
model is never applied. The same applies to `valor resume`: the granite
container has no `--resume` wiring, so a "resumed" session re-runs in a
fresh TUI without the prior transcript. Both are substrate gaps, not
wrapper gaps — tracked in the granite production cutover doc's Known
Limitations.

### 9. Wrapper test coverage

`tests/unit/test_valor_cli.py` covers the positional-shortcut rewrite, the
allowlist/parser parity, the per-subcommand namespace translation (every
`cmd_*` attribute the underlying CLI reads), the missing-prompt error, and
the help paths. End-to-end behavior (enqueue, worker pickup) is still
covered only by the `valor-session` integration tests plus manual smoke
tests.

## Tests Run on the Branch

| Scope | Result |
|-------|--------|
| `tests/unit/granite_container/` (BridgeAdapter, PTYPool, container, classifier, persona priming) | 143 passed, 5 skipped |
| `tests/integration/test_granite_container_loop.py` + `test_granite_pty_production.py` | 3 passed, 1 skipped |
| `tests/unit/test_session_executor_granite.py` | green |
| `tests/unit/test_agent_session_queue.py` | green |
| Wider unit suite | 36 pre-existing failures (memory model, work request classifier, media handling) — confirmed on main, out of scope for this branch |

The wrapper is a 267-line change with no runtime behavior change, so the only
test signal that matters is the smoke test. The pre-existing 36 failures are
flagged in the wider suite for a future cleanup pass.

## Related Documentation

- [Session Steering](session-steering.md) — `valor-session` CLI for create/steer/status/list/kill
- [Agent Session Queue](agent-session-queue.md) — Queue dispatch surface underneath the wrapper
- [Granite PTY Production Cutover](granite-pty-production.md) — The execution substrate the new sessions run on
- [Eng Session Architecture](eng-session-architecture.md) — How PM and Dev sessions interact
