# `valor` CLI: The Agent-Session Wrapper

**Status**: Shipped (PR #1612, on `session/granite-pty-production-cutover`)

## Problem

`valor-session` is the canonical interface for managing AgentSessions, but its
CLI is verbose for the common case. Spinning up a session that "just does this
thing" requires:

```bash
valor-session create --role pm --message "fix the typo in app.py"
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
# Create — single positional prompt, defaults to PM role.
# PM and dev sessions REQUIRE a slug: pass --slug, or include
# "issue #N" in the prompt so the slug auto-derives to sdlc-N
# (issues #1109 / #1272 — slugless invocations exit 1).
valor "plan issue #1615"
valor "fix the typo in app.py" --slug typo-fix
valor agent-session --role dev --model sonnet --slug feature-x "build the feature"

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
valor "smoke test: confirm the valor CLI wrapper is on the cutover branch" --role dev --slug valor-smoke
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

### 1. The `valor` shell alias still shadows the venv binary

The user's zsh `alias valor='cd /Users/valorengels/src/ai && ./scripts/telegram_run.sh'`
points to a script that no longer exists. Running `valor` in a fresh shell
errors with `./scripts/telegram_run.sh: No such file or directory`. The fix is
to either (a) drop the alias, (b) point it at `.venv/bin/valor`, or (c) prepend
`.venv/bin` to PATH ahead of alias resolution. The wrapper does not address
this — it works when called as `python -m tools.valor_cli ...` or
`/Users/valorengels/src/ai/.venv/bin/valor ...`, but the bare `valor` command
is still broken in shells that load the stale alias.

### 2. The positional-shortcut disambiguation is a literal allowlist

The first argv token is compared against the module-level set:

```python
KNOWN_SUBCOMMANDS = {"agent-session", "list", "status", "steer", "kill",
                     "resume", "inspect", "children", "release"}
```

If a future subcommand is added to the wrapper, it must be appended here or
the new name will be silently rewritten to `agent-session` (`valor foo bar`
becomes `valor agent-session foo bar`). The set duplicates the subparser
declarations by design (it must be consulted before argparse runs);
`tests/unit/test_valor_cli.py::test_known_subcommands_matches_parser`
asserts the two stay in sync, so drift now fails CI instead of failing a
user.

### 3. Worker pre-flight check uses a stale Redis cache

`valor-session create` calls `_check_worker_health()` which reads a worker
heartbeat key. After a worker restart, the key can lag the actual process
state by 30-60 seconds. The wrapper surfaces the same warning:

```
WARNING: no active worker detected — session will stay pending until a worker is started
```

Even when the worker is running and healthy (verified via the dashboard
`/dashboard.json` endpoint), the CLI can refuse to enqueue. The session does
get created — the warning is misleading. A better signal would be a single
"ready" check against the dashboard, not a Redis heartbeat read.

### 4. No help text on the positional shortcut

`valor --help` and `valor agent-session --help` work fine. But
`valor "fix the bug" --help` does not — argparse sees the prompt as a
positional and the help flag is consumed. The shortcut only fires when no
flag is present. Users who expect `valor` to always show help for an unknown
flag will be surprised. A `--help` short-circuit before positional injection
would fix it but at the cost of a special case.

### 5. The wrapper does not bundle the granite-pty path as a default

The wrapper is execution-agnostic: it creates a session, the worker decides
how to run it. The new granite PTY substrate (`agent/granite_container/`)
is selected by the worker's session executor based on environment, not by
the CLI. This is the right separation of concerns, but it means calling
`valor` does not guarantee a PTY-backed session — only that a session is
enqueued. If a future env flag is needed to force the new path, the wrapper
should expose it; right now the PTY path is implicit.

### 6. Pre-commit hook guard #1288 still requires worktree-local commits

The pre-commit hook blocks `git commit` on `session/*` branches from outside
`.worktrees/{slug}/`. So even with the wrapper, the development loop is:

```bash
# in main checkout
git checkout session/granite-pty-production-cutover   # bound to main checkout
# ...make changes...
git worktree add .worktrees/granite-pty-production-cutover session/granite-pty-production-cutover
# copy changes in, commit from worktree
```

The wrapper does not change this. The workflow is documented in the #1288
guard, but a future improvement could let `valor` itself do the
worktree-attach dance before delegating to `valor-session create` for
sub-session work.

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
- [PM/Dev Session Architecture](pm-dev-session-architecture.md) — How PM and Dev sessions interact
