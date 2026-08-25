# `valor` CLI: The Agent-Session Wrapper

`valor` is a thin delegation wrapper around the `valor-session` CLI. It lives at
`tools/valor_cli.py` and is installed as `valor` in `pyproject.toml`
`[project.scripts]`. Every subcommand forwards to the matching `cmd_*` function
in `tools.valor_session` and exits with the same return code — no new
abstractions, no duplicated state, no schema changes.

## Interface

```bash
# Create — single positional prompt, defaults to the eng role.
# Eng and PM sessions require a slug: pass --slug, or include
# "issue #N" in the prompt so the slug auto-derives to sdlc-N
# (slugless invocations exit 1).
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
valor kill --all                    # kill every session

# Inspection / repair
valor inspect <id>                  # raw fields
valor progress <id>                 # read-only liveness verdict
valor children <id>                 # child sessions
valor resume <id> "new message"     # resume a session
valor release --pr 1615             # clear retain_for_resume after PR merge/close
```

Two equivalent ways to create a session:

- `valor "fix the bug" --slug fix-bug` — positional shortcut (preferred for humans and agents)
- `valor agent-session "fix the bug" --slug fix-bug` — explicit subcommand (preferred for scripts)

## How It Works

### Positional-shortcut detection

If the first token is not a known subcommand or a flag, it is prepended with
`agent-session` before argparse runs. The first token is checked against the
module-level `KNOWN_SUBCOMMANDS` set (anything starting with `-` is excluded by
the flag check first). `KNOWN_SUBCOMMANDS` is derived at import time from the
registered subparsers via `_derive_known_subcommands()`, which reads
`_SubParsersAction.choices.keys()`; `_build_parser()` is decorated with
`@functools.lru_cache` so the import-time derivation and every runtime `main()`
call share a single parser build. Adding a subparser automatically extends the
allowlist — there is no parallel literal to keep in sync.

### Argparse namespace translation

Each subcommand has a `_to_<cmd>_namespace` helper that copies fields from the
`valor` argparse `Namespace` into the shape `valor-session` expects. The helpers
are mechanical, so a renamed flag on the underlying CLI shows up in exactly one
helper.

### Help short-circuit

`main()` runs a help short-circuit on the pre-rewrite argv (before positional
injection). It fires when the first token is a bare prompt (not a flag, not a
known subcommand) and a standalone `-h`/`--help` token appears anywhere in
`argv`. It then prints top-level `valor --help` and raises `SystemExit(0)`.
"Standalone token" means an exact `argv` element equal to `-h` or `--help`, not
a substring — `valor "document the --help flag"` is a single element and does
not trigger the guard.

### Worker pre-flight

`_check_worker_health()` resolves the heartbeat file via
`git rev-parse --path-format=absolute --git-common-dir` (with a
`__file__`-relative fallback), so the correct file is found from any worktree.
The down threshold is `WORKER_DOWN_THRESHOLD_S = 600` (2× the write cadence) in
`agent/constants.py`, shared by `valor-session` and
`tools/agent_session_scheduler.py`. The `--json` output from `cmd_create` and
`cmd_status` carries `worker_state` ("ok" or "down") and
`worker_heartbeat_age_s` (the raw age in seconds, clamped to 0) alongside
`worker_healthy`. The warning never claims a created session will not run.

### Alias-shadow check

`check_valor_alias_shadow()` in `scripts/update/verify.py` runs on every
machine during `/update` and warns if `~/.zshrc` contains a non-comment line
matching `^\s*alias\s+valor\s*=`, with a copy-paste fix in the message.

## Design Boundary

The CLI's job is to enqueue a session. The worker selects the execution
substrate (the [headless session runner](headless-session-runner.md),
`agent/session_runner/`) — not the CLI. The headless session runner is the only
execution substrate; `valor "do the thing"` guarantees a session is enqueued and
run through it.

## Known Limitations

### Eng and PM sessions require a slug

The default role is `pm`, and `cmd_create` rejects slugless PM/dev sessions. The
shortest honest create is `valor "plan issue #1615"` (slug auto-derives from
`issue #N` in the prompt) or `valor "do the thing" --slug thing`. Only
`--role teammate` works with a bare prompt. The failure is `exit 1` with a
stderr explanation.

### Per-session `--model` is not applied by the session runner

`valor agent-session --model sonnet ...` stores `model` on the AgentSession and
the executor resolves it (`_resolve_session_model`), but each turn spawns via
`SessionRunnerSettings.pm_model` / `dev_model` (fixed per-process config), so the
per-session value is never applied.

`valor resume` persists the four resume scalars (`claude_session_uuid`,
`dev_agent_id`, `runner_cwd`, `claude_version`) and a resumed session continues
the same Claude session with its prior transcript intact. See
[Headless Session Runner](headless-session-runner.md).

## Test Coverage

`tests/unit/test_valor_cli.py` covers the positional-shortcut rewrite, the
allowlist/parser parity, the per-subcommand namespace translation, the
missing-prompt error, and the help paths. End-to-end behavior (enqueue, worker
pickup) is covered by the `valor-session` integration tests plus manual smoke
tests.

## Related Documentation

- [Session Steering](session-steering.md) — `valor-session` CLI for create/steer/status/list/kill
- [Agent Session Queue](agent-session-queue.md) — queue dispatch surface underneath the wrapper
- [Headless Session Runner](headless-session-runner.md) — the execution substrate new sessions run on
- [Eng Session Architecture](eng-session-architecture.md) — how PM and Dev sessions interact
